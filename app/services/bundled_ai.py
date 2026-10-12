"""Verified, immutable Complete Edition seed; mutable models remain user-owned."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import sys
import tempfile

from app.services.ai_pack_storage import AIPackStorage
from app.services.runtime_profiles import RUNTIME_PROFILES
from app.services.app_paths import portable_app_path

MANIFEST = 'bundle_manifest.json'


def _arch(value):
    return {'x86_64':'amd64','x64':'amd64','aarch64':'arm64'}.get(value.lower(), value.lower())


def validate_pack(root, target=None, machine=None, cancel_event=None):
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("Complete validation cancelled")
    root = Path(root)
    for path in (root, *root.parents):
        AIPackStorage._links(path, allow_system_alias=True)
    manifest_path = root / MANIFEST
    if manifest_path.stat().st_size > 1024 * 1024:
        raise ValueError('Complete manifest is too large')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('schema_version') != 1 or manifest.get('engine') != 'ollama':
        raise ValueError('Complete requires an Ollama pack manifest version 1')
    if manifest.get('platform') != (target or sys.platform) or manifest.get('architecture') != _arch(machine or platform.machine()):
        raise ValueError('Complete pack does not match the target platform')
    AIPackStorage._name(manifest.get('model'))
    if manifest.get('runtime_profile', 'default') not in RUNTIME_PROFILES:
        raise ValueError('Unknown Complete runtime profile')
    for field in ('license', 'engine_version', 'source'):
        if not isinstance(manifest.get(field), str) or not manifest[field].strip():
            raise ValueError('Complete pack requires license, source and engine_version')
    files = manifest.get('files')
    if not isinstance(files, dict) or not files:
        raise ValueError('Complete pack requires a file inventory')
    actual = set()
    for path in root.rglob('*'):
        AIPackStorage._links(path)
        if path.is_file() and path != manifest_path:
            actual.add(path.relative_to(root).as_posix())
    if actual != set(files):
        raise ValueError('Complete pack contains missing or unlisted files')
    for relative, expected in files.items():
        path = PurePosixPath(relative)
        if path.is_absolute() or '..' in path.parts or '\\' in relative or str(path) != relative:
            raise ValueError('Unsafe Complete pack path')
        if path.name.casefold() in {'notes.db','notes.sqlite3','settings.json','ai_settings.json'} or path.name.casefold().endswith(('.db','.sqlite','.sqlite3','-wal','-shm')):
            raise ValueError('User data cannot be bundled in Complete')
        candidate = root / relative
        if not candidate.resolve().is_relative_to(root.resolve()):
            raise ValueError('Unsafe Complete pack path')
        if not isinstance(expected, dict) or type(expected.get('size')) is not int or expected['size'] != candidate.stat().st_size:
            raise ValueError('Complete pack size mismatch')
        digest = hashlib.sha256()
        with candidate.open('rb') as source:
            for block in iter(lambda: source.read(1024*1024), b''):
                if cancel_event is not None and cancel_event.is_set():
                    raise InterruptedError('Complete validation cancelled')
                digest.update(block)
        if digest.hexdigest() != expected.get('sha256'):
            raise ValueError('Complete pack checksum mismatch')
    executable = 'runtime/ollama/ollama.exe' if (target or sys.platform) == 'win32' else 'runtime/ollama/ollama'
    if executable not in files:
        raise ValueError('Complete pack requires the native Ollama executable')
    if (target or sys.platform) != 'win32' and not os.access(root/executable, os.X_OK):
        raise ValueError('Complete runtime is not executable')
    model, tag = AIPackStorage._name(manifest['model'])
    model_manifest = f'models/manifests/registry.ollama.ai/library/{model}/{tag}'
    if model_manifest not in files or not any(p.startswith('models/blobs/') for p in files):
        raise ValueError('Complete pack requires installed model manifests and weights')
    references = json.loads((root/model_manifest).read_text(encoding='utf-8'))
    for item in [references.get('config', {}), *references.get('layers', [])]:
        digest = item.get('digest', '')
        if not digest.startswith('sha256:') or 'models/blobs/'+digest.replace(':','-') not in files:
            raise ValueError('Complete model references missing weights')
    return manifest


def find_complete_seed(bundle, cancel_event=None):
    bundle = Path(bundle)
    seeds = [seed for seed in (bundle/'bundled_ai', bundle/'_internal/bundled_ai',
                              bundle/'Contents/Resources/bundled_ai')
             if seed.exists() or seed.is_symlink()]
    if len(seeds) > 1:
        raise ValueError('Ambiguous Complete seeds')
    return (seeds[0], validate_pack(seeds[0],cancel_event=cancel_event)) if seeds else None


def _empty_managed_destination(destination):
    """Accept only an empty directory or exact application ownership markers."""
    storage=AIPackStorage(destination)
    storage._safe(destination)
    if not destination.exists():return True
    entries={path.name for path in destination.iterdir()}
    if not entries:return True
    if not entries.issubset({AIPackStorage.MARKER, 'models'}) or AIPackStorage.MARKER not in entries:
        return False
    storage._check()
    models=destination/'models'
    if models.exists():
        AIPackStorage(models)._check()
        if {path.name for path in models.iterdir()} != {AIPackStorage.MARKER}:
            return False
    return True


_RESUME_MARKER='.notes-complete-seed'


def _complete_config(manifest, destination):
    return {'provider':'ollama', 'model':manifest['model'], 'managed_pack':True,
            'runtime_profile':manifest.get('runtime_profile', 'default'),
            'runtime_root':portable_app_path(destination), 'ai_pack_path':portable_app_path(destination),
            'onboarding_seen':True, 'base_url':'http://127.0.0.1:11434',
            'embedding_model':manifest.get('embedding_model', 'qwen3-embedding:0.6b')}


def _resume_complete(manifest, destination, cancel_event):
    marker=destination/_RESUME_MARKER
    if not marker.exists():return False
    AIPackStorage(destination)._check()
    AIPackStorage(destination/'models')._check()
    fingerprint=hashlib.sha256(json.dumps(manifest,sort_keys=True).encode('utf-8')).hexdigest()
    if marker.read_text(encoding='utf-8') != fingerprint:return False
    inventory={name:expected for name,expected in manifest['files'].items()
               if name.startswith(('models/', 'runtime/'))}
    actual={path.relative_to(destination).as_posix() for directory in ('models','runtime')
            for path in (destination/directory).rglob('*')
            if path.is_file() and path.name != AIPackStorage.MARKER}
    if actual != set(inventory):return False
    for name,expected in inventory.items():
        path=destination/name
        if path.stat().st_size!=expected['size']:return False
        digest=hashlib.sha256()
        with path.open('rb') as source:
            for block in iter(lambda:source.read(1024*1024),b''):
                if cancel_event is not None and cancel_event.is_set():
                    raise InterruptedError('Complete installation cancelled')
                digest.update(block)
        if digest.hexdigest()!=expected['sha256']:return False
    return True


def install_complete_seed(seed, data_root, cancel_event=None):
    """Run in a worker. Never overwrite user configuration or an existing store."""
    manifest = validate_pack(seed,cancel_event=cancel_event)
    destination = Path(data_root)/'ai'
    safety = AIPackStorage(destination)
    safety._safe(destination)
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError('Complete installation cancelled')
    if _resume_complete(manifest,destination,cancel_event):
        return _complete_config(manifest,destination)
    if not _empty_managed_destination(destination):
        raise ValueError('Existing AI-pack is preserved; select it in settings')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.complete-', dir=destination.parent))
    try:
        for directory in ('models', 'runtime'):
            for source in (Path(seed)/directory).rglob('*'):
                if cancel_event is not None and cancel_event.is_set():
                    raise InterruptedError('Complete installation cancelled')
                relative = source.relative_to(seed)
                target = temporary/relative
                if source.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    AIPackStorage._links(source)
                    copied_hash=hashlib.sha256()
                    copied_size=0
                    with source.open('rb') as incoming, target.open('xb') as outgoing:
                        for block in iter(lambda: incoming.read(1024*1024), b''):
                            if cancel_event is not None and cancel_event.is_set():
                                raise InterruptedError('Complete installation cancelled')
                            outgoing.write(block)
                            copied_hash.update(block)
                            copied_size+=len(block)
                    expected=manifest['files'][relative.as_posix()]
                    if copied_hash.hexdigest()!=expected['sha256'] or copied_size!=expected['size']:
                        raise ValueError('Complete source changed while copying')
                    shutil.copystat(source, target)
        for directory in (temporary, temporary/'models'):
            (directory/AIPackStorage.MARKER).write_text(AIPackStorage.MARKER_CONTENT, encoding='utf-8')
        (temporary/_RESUME_MARKER).write_text(hashlib.sha256(json.dumps(manifest,sort_keys=True).encode('utf-8')).hexdigest(),encoding='utf-8')
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError('Complete installation cancelled')
        if not _empty_managed_destination(destination):
            raise ValueError('AI-pack changed while installing; user data preserved')
        backup=None
        if destination.exists():
            import uuid
            backup=destination.with_name('.complete-empty-'+uuid.uuid4().hex)
            destination.rename(backup)
        try:
            temporary.rename(destination)
        except BaseException:
            if backup is not None:backup.rename(destination)
            raise
        if backup is not None:
            shutil.rmtree(backup)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return _complete_config(manifest,destination)
