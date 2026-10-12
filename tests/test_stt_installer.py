import hashlib
import io
import json
import threading
import zipfile

import pytest
from app.services import stt_installer as stt


def test_manual_install_is_portable_and_copies_files(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    executable = source / 'whisper-cli'
    executable.write_bytes(b'engine')
    model = source / 'model.bin'
    model.write_bytes(b'model')
    manager = stt.STTManager(tmp_path / 'app' / 'stt')
    assert manager.save_manual_paths(executable, model)['ready']
    config = json.loads((manager.root / 'components.json').read_text())
    assert not stt.Path(config['executable']).is_absolute()
    executable.unlink()
    model.unlink()
    assert manager.status()['ready']
    manager.component_paths()[1].write_bytes(b'corrupt')
    assert not manager.status()['ready']


def test_cancel_preserves_existing_configuration(tmp_path, monkeypatch):
    manager = stt.STTManager(tmp_path / 'stt')
    monkeypatch.setattr(stt.platform, 'system', lambda: 'Windows')
    monkeypatch.setattr(stt.platform, 'machine', lambda: 'AMD64')
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(stt.STTError, match='отменена'):
        manager.install(cancelled)
    assert not manager.root.exists()


@pytest.mark.parametrize('name', ['../escape', '/escape', 'C:/escape', 'folder/../../escape', '..\\escape'])
def test_archive_traversal_rejected(tmp_path, name):
    manager = stt.STTManager(tmp_path / 'stt')
    manager.root.mkdir()
    archive = manager.root / 'bad.zip'
    with zipfile.ZipFile(archive, 'w') as bundle:
        bundle.writestr(name, b'data')
    with pytest.raises(stt.STTError):
        manager._extract(archive, manager.root / 'engine', threading.Event())


def test_mock_download_checksum_and_close(tmp_path, monkeypatch):
    content = b'checked model'
    class Response(io.BytesIO):
        headers = {'Content-Length': str(len(content))}
        def geturl(self):
            return 'https://huggingface.co/file'
    response = Response(content)
    class Opener:
        def open(self, request, timeout):
            assert timeout == 16
            return response
    monkeypatch.setattr(stt.urllib.request, 'build_opener', lambda *args: Opener())
    manager = stt.STTManager(tmp_path)
    specification = dict(url='https://huggingface.co/file', size=len(content), sha256=hashlib.sha256(content).hexdigest())
    manager._download(specification, tmp_path / 'model.part', threading.Event(), None, 0, len(content))
    assert response.closed
    assert (tmp_path / 'model.part').read_bytes() == content


@pytest.mark.parametrize('url', ['http://github.com/a', 'https://evil.example/a', 'https://github.com:444/a', 'https://user@github.com/a'])
def test_download_hosts_restricted(url):
    with pytest.raises(stt.STTError):
        stt._validate_url(url)


def test_symlink_root_rejected(tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    linked = tmp_path / 'linked'
    try:
        linked.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip('Symlink creation requires system permission')
    with pytest.raises(stt.STTError):
        stt.STTManager(linked)._safe(linked / 'model')


def test_mac_auto_install_reports_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(stt.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(stt.shutil, 'which', lambda name: None)
    manager = stt.STTManager(tmp_path / 'stt')
    assert not manager.status()['supported']
    with pytest.raises(stt.STTError, match='недоступна'):
        manager.install()


def test_invalid_integrity_metadata_is_not_ready(tmp_path):
    manager = stt.STTManager(tmp_path)
    executable = tmp_path / 'whisper-cli'
    executable.write_bytes(b'engine')
    model = tmp_path / 'model.bin'
    model.write_bytes(b'model')
    manager._write_config(dict(executable='whisper-cli', model='model.bin', files=[]))
    assert not manager.status()['ready']


def test_manual_import_errors_are_safe(tmp_path):
    manager = stt.STTManager(tmp_path / 'stt')
    with pytest.raises(stt.STTError):
        manager.save_manual_paths(tmp_path / 'missing', tmp_path / 'missing-model')
    assert not manager.root.exists()


def test_metadata_traversal_rejected(tmp_path):
    root = tmp_path / 'stt'
    root.mkdir()
    manager = stt.STTManager(root)
    (tmp_path / 'outside').write_bytes(b'private')
    manager._write_config(dict(executable='../outside', model='../outside', files={}))
    assert manager.component_paths() == (None, None)
    assert not manager.status()['ready']


def test_macos_build_requires_tools(tmp_path, monkeypatch):
    monkeypatch.setattr(stt.shutil, 'which', lambda name: None)
    with pytest.raises(stt.STTError, match='CMake'):
        stt.STTManager(tmp_path)._build_macos(tmp_path / 'source', tmp_path / 'engine', threading.Event())


def test_macos_source_install_mock(tmp_path, monkeypatch):
    monkeypatch.setattr(stt.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(stt.shutil, 'which', lambda name: '/usr/bin/' + name)
    manager = stt.STTManager(tmp_path / 'stt')
    def download(specification, target, *args):
        target.write_bytes(b'checked')
    def extract(archive, target, cancelled):
        target.mkdir()
    def build(source, target, cancelled):
        target.mkdir()
        (target / 'whisper-cli').write_bytes(b'engine')
        source.rmdir()
    monkeypatch.setattr(manager, '_download', download)
    monkeypatch.setattr(manager, '_extract', extract)
    monkeypatch.setattr(manager, '_build_macos', build)
    assert manager.install()['ready']
    assert manager.component_paths()[0].name == 'whisper-cli'


def test_official_hf_cdn_redirect_allowed():
    stt._validate_url('https://us.aws.cdn.hf.co/model.bin')
