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


def test_multilingual_base_is_verified_default():
    assert stt.MODEL is stt.MODELS['base']
    assert stt.MODEL['size'] == 147951465
    assert stt.MODEL['sha256'] == '60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe'
    assert stt.MODEL['url'].endswith('/5359861c739e955e79d9a303bcbc70fb988958b1/ggml-base.bin')


def test_unknown_model_cannot_change_existing_install(tmp_path):
    manager = stt.STTManager(tmp_path / 'stt')
    with pytest.raises(stt.STTError, match='Неизвестная'):
        manager.install(model_name='../outside')
    assert not manager.root.exists()


def test_legacy_tiny_remains_ready(tmp_path):
    manager = stt.STTManager(tmp_path)
    (tmp_path / 'whisper-cli').write_bytes(b'engine')
    (tmp_path / 'ggml-tiny.bin').write_bytes(b'model')
    manager._write_config(dict(manual=False, executable='whisper-cli', model='ggml-tiny.bin',
                               files={name: stt._hash(tmp_path / name) for name in ['whisper-cli', 'ggml-tiny.bin']}))
    assert manager.status()['ready']
    assert manager.status()['model_variant'] == 'tiny-q5_1'


@pytest.mark.parametrize('variant', [None, 'base-q5_1', 'tiny-q5_1', 'small-q5_1', 'small'])
def test_model_selection_records_actual_variant(tmp_path, monkeypatch, variant):
    monkeypatch.setattr(stt.platform, 'system', lambda: 'Windows')
    monkeypatch.setattr(stt.platform, 'machine', lambda: 'AMD64')
    manager = stt.STTManager(tmp_path / 'stt')
    downloaded = []
    def download(specification, target, *args):
        downloaded.append(specification)
        target.write_bytes(b'checked')
    def extract(archive, target, cancelled):
        target.mkdir()
        (target / 'whisper-cli.exe').write_bytes(b'engine')
    monkeypatch.setattr(manager, '_download', download)
    monkeypatch.setattr(manager, '_extract', extract)
    result = manager.install(model_name=variant)
    expected = variant or 'base'
    assert result['ready'] and result['model_variant'] == expected
    assert downloaded[1] == stt.MODELS[expected]
    assert result['model'].name == 'ggml-' + expected + '.bin'


def make_library_tar(path, links):
    import tarfile
    with tarfile.open(path, 'w:gz') as bundle:
        regular = tarfile.TarInfo('pack/libvoice.so.1.2')
        payload = b'verified runtime library'
        regular.size = len(payload)
        regular.mode = 0o755
        bundle.addfile(regular, io.BytesIO(payload))
        for name, target in links:
            alias = tarfile.TarInfo(name)
            alias.type = tarfile.SYMTYPE
            alias.linkname = target
            bundle.addfile(alias)


def test_tar_internal_library_aliases_are_materialized(tmp_path):
    manager = stt.STTManager(tmp_path)
    archive = tmp_path / 'engine.tar.gz'
    make_library_tar(archive, [('pack/libvoice.so', 'libvoice.so.1'), ('pack/libvoice.so.1', 'libvoice.so.1.2')])
    manager._extract(archive, tmp_path / 'engine', threading.Event())
    for filename in ['libvoice.so', 'libvoice.so.1', 'libvoice.so.1.2']:
        path = tmp_path / 'engine' / 'pack' / filename
        assert path.read_bytes() == b'verified runtime library'
        assert not path.is_symlink()


@pytest.mark.parametrize('target', ['/outside', '../../outside', 'C:/outside', 'missing.so', 'libvoice.so'])
def test_tar_unsafe_aliases_are_rejected(tmp_path, target):
    archive = tmp_path / 'bad.tar.gz'
    make_library_tar(archive, [('pack/libvoice.so', target)])
    with pytest.raises(stt.STTError):
        stt.STTManager(tmp_path)._extract(archive, tmp_path / 'engine', threading.Event())


def test_tar_hard_links_remain_rejected(tmp_path):
    import tarfile
    archive = tmp_path / 'bad.tar.gz'
    with tarfile.open(archive, 'w:gz') as bundle:
        member = tarfile.TarInfo('hardlink')
        member.type = tarfile.LNKTYPE
        member.linkname = 'somefile'
        bundle.addfile(member)
    with pytest.raises(stt.STTError):
        stt.STTManager(tmp_path)._extract(archive, tmp_path / 'engine', threading.Event())


def test_system_ancestor_alias_allowed_but_internal_alias_rejected(tmp_path):
    actual = tmp_path / 'actual-system-directory'
    actual.mkdir()
    alias = tmp_path / 'system-alias'
    try:
        alias.symlink_to(actual, target_is_directory=True)
    except OSError:
        pytest.skip('Symlink creation requires system permission')
    root = alias / 'trusted-app' / 'stt'
    root.mkdir(parents=True)
    manager = stt.STTManager(root)
    assert manager._safe(root / 'model.bin') == root.resolve() / 'model.bin'
    outside = tmp_path / 'outside'
    outside.mkdir()
    (root / 'linked-models').symlink_to(outside, target_is_directory=True)
    with pytest.raises(stt.STTError):
        manager._safe(root / 'linked-models' / 'model.bin')


@pytest.mark.skipif(stt.platform.system() != 'Darwin', reason='Native macOS system alias contract')
def test_native_macos_var_system_alias_is_allowed():
    import tempfile
    with tempfile.TemporaryDirectory(dir='/var/tmp') as temporary:
        root = stt.Path(temporary) / 'OrangeNotes' / 'stt'
        root.mkdir(parents=True)
        manager = stt.STTManager(root)
        assert manager._safe(root / 'model.bin').resolve().is_relative_to(root.resolve())
