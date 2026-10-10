"""Conservative, application-owned Ollama model storage.

No arbitrary directory is adopted. Unsafe links anywhere in the managed tree
make all operations fail closed; malformed manifests prevent blob deletion.
"""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import uuid


class StorageSafetyError(ValueError):
    pass


@dataclass(frozen=True)
class AIPackEntry:
    name: str
    size_bytes: int
    reclaimable_bytes: int


@dataclass
class StorageSnapshot:
    root: str
    token: str
    existing: frozenset
    owned: dict = field(default_factory=dict)


class AIPackStorage:
    MARKER = '.notes-ai-pack-owner'
    MARKER_CONTENT = 'notes-app-ai-pack-v1\n'

    def __init__(self, root):
        self.root = Path(os.path.abspath(os.fspath(root)))
        self._operations = {}

    @staticmethod
    def _links(path):
        try:
            info = path.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise StorageSafetyError('Links and Windows reparse points are forbidden')

    def _safe(self, path):
        path = Path(path)
        if not path.is_absolute():
            path = self.root / path
        for ancestor in (path, *path.parents):
            self._links(ancestor)
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise StorageSafetyError('Path is outside managed storage')
        return path

    def _check(self):
        self._safe(self.root)
        marker = self._safe(self.root / self.MARKER)
        if not marker.is_file() or marker.read_text(encoding='utf-8') != self.MARKER_CONTENT:
            raise StorageSafetyError('Storage has not been initialized by this application')
        for directory, dirs, files in os.walk(self.root, followlinks=False):
            for name in dirs + files:
                self._safe(Path(directory) / name)

    def initialize(self):
        self._safe(self.root)
        if self.root.exists():
            marker = self.root / self.MARKER
            if marker.exists():
                self._check()
                return self.root
            if any(self.root.iterdir()):
                raise StorageSafetyError('Refusing to adopt an existing nonempty directory')
        self.root.mkdir(parents=True, exist_ok=True)
        marker = self._safe(self.root / self.MARKER)
        with marker.open('x', encoding='utf-8') as stream:
            stream.write(self.MARKER_CONTENT)
        return self.root

    @staticmethod
    def _name(name):
        if not isinstance(name, str) or not name or '\\' in name or '/' in name:
            raise ValueError('Invalid model name')
        parts = name.split(':')
        if len(parts) > 2 or any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', p) or '..' in p for p in parts):
            raise ValueError('Invalid model name')
        return parts[0], parts[1] if len(parts) == 2 else 'latest'

    def _manifests(self):
        base = self.root / 'manifests'
        records = {}
        if not base.exists():
            return records
        for path in base.rglob('*'):
            if not path.is_file():
                continue
            try:
                manifest = json.loads(path.read_text(encoding='utf-8'))
                references = set()
                for item in [manifest.get('config', {}), *manifest.get('layers', [])]:
                    digest = item.get('digest')
                    if digest is None:
                        continue
                    if not re.fullmatch(r'sha256:[a-fA-F0-9]{64}', digest):
                        raise ValueError('Invalid blob digest')
                    references.add(digest.replace(':', '-'))
                records[path] = references
            except (OSError, ValueError, TypeError, AttributeError) as exc:
                raise StorageSafetyError('Cannot safely read all model manifests') from exc
        return records

    def scan(self):
        self._check()
        records = self._manifests()
        base = self.root / 'manifests' / 'registry.ollama.ai' / 'library'
        entries = []
        for path, refs in records.items():
            if not path.is_relative_to(base) or len(path.relative_to(base).parts) != 2:
                continue
            model, tag = path.relative_to(base).parts
            self._name(model + ':' + tag)
            others = set().union(*(v for p, v in records.items() if p != path))
            sizes = {blob: self._safe(self.root / 'blobs' / blob).stat().st_size
                     for blob in refs if (self.root / 'blobs' / blob).is_file()}
            entries.append(AIPackEntry(model + ':' + tag, sum(sizes.values()),
                                      path.stat().st_size + sum(size for blob, size in sizes.items() if blob not in others)))
        return sorted(entries, key=lambda entry: entry.name)

    def _unlink(self, path):
        self._check()
        path = self._safe(path)
        size = path.stat().st_size
        path.unlink()
        return size

    def delete_model(self, name):
        model, tag = self._name(name)
        self._check()
        records = self._manifests()
        path = self._safe(self.root / 'manifests' / 'registry.ollama.ai' / 'library' / model / tag)
        if path not in records:
            return 0
        others = set().union(*(v for p, v in records.items() if p != path))
        removable = records[path] - others
        freed = self._unlink(path)
        for blob in removable:
            target = self._safe(self.root / 'blobs' / blob)
            if target.is_file():
                freed += self._unlink(target)
        return freed

    def begin_operation(self):
        self._check()
        snapshot = StorageSnapshot(str(self.root), uuid.uuid4().hex,
                                   frozenset(p.relative_to(self.root).as_posix() for p in self.root.rglob('*')))
        self._operations[snapshot.token] = snapshot
        return snapshot

    def register_partial(self, snapshot, relative_path):
        """Claim an incomplete file created by this operation, after creation.

        Existing files and complete blobs cannot be claimed. The caller must
        register only files it created itself, never infer ownership from a scan.
        """
        self._check()
        if self._operations.get(snapshot.token) is not snapshot:
            raise StorageSafetyError('Unknown storage operation')
        raw = str(relative_path)
        parts = PurePosixPath(raw).parts
        if '\\' in raw or ':' in raw or raw.startswith('/') or '..' in parts:
            raise StorageSafetyError('Invalid partial path')
        path = self._safe(self.root / raw)
        if len(parts) != 2 or parts[0] != 'blobs' or not re.fullmatch(r'sha256-[a-fA-F0-9]{64}-partial(?:-[0-9]+)?', parts[1]):
            raise StorageSafetyError('Only incomplete Ollama blobs can be claimed')
        if raw in snapshot.existing or not path.is_file():
            raise StorageSafetyError('Partial file was not newly created')
        info = path.stat()
        snapshot.owned[raw] = (info.st_dev, info.st_ino)

    def cleanup_partial(self, snapshot):
        self._check()
        if self._operations.get(snapshot.token) is not snapshot or snapshot.root != str(self.root):
            raise StorageSafetyError('Unknown storage operation')
        freed = 0
        for raw, identity in snapshot.owned.items():
            path = self._safe(self.root / raw)
            if path.exists():
                info = path.stat()
                if (info.st_dev, info.st_ino) == identity:
                    freed += self._unlink(path)
        del self._operations[snapshot.token]
        return freed
