"""Verified, isolated installation of optional CPU speech recognition components."""
import hashlib
import json
import os
import platform
import shutil
import stat
import signal
import subprocess
import time
import tarfile
import tempfile
import threading
import urllib.request
from pathlib import Path, PurePosixPath
import zipfile

from .local_stt import STTError

MODEL = dict(url='https://huggingface.co/ggerganov/whisper.cpp/resolve/5359861c739e955e79d9a303bcbc70fb988958b1/ggml-tiny-q5_1.bin', size=32152673, sha256='818710568da3ca15689e31a743197b520007872ff9576237bda97bd1b469c3d7')
MAC_SOURCE = dict(url='https://codeload.github.com/ggml-org/whisper.cpp/tar.gz/refs/tags/v1.9.5', size=9626464, sha256='ff1a9053feb509ff9d7729703355541ae9690073a6b1c40eb692c962e0dc1720')
ENGINES = {
    'Windows': dict(url='https://github.com/ggml-org/whisper.cpp/releases/download/b5454/whisper-bin-x64.zip', size=8928640, sha256='6ba69e3482d7826214f90a6a9c84ca07782aec1e1d0c6a7c30c994fd5d816ccb'),
    'Linux': dict(url='https://github.com/ggml-org/whisper.cpp/releases/download/b5454/whisper-bin-ubuntu-x64.tar.gz', size=10364195, sha256='a72becf15d7917f990f6313867a52638b82b7f9ef237fb0c980dac56a135781c'),
}
HOSTS = {'github.com', 'codeload.github.com', 'release-assets.githubusercontent.com', 'huggingface.co', 'cdn-lfs.huggingface.co', 'cdn-lfs.hf.co', 'cas-bridge.xethub.hf.co', 'us.aws.cdn.hf.co'}


def _validate_url(url):
    from urllib.parse import urlsplit
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in HOSTS or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise STTError('Недопустимый адрес загрузки голосового ввода.')


class _Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _linked(path):
    return path.is_symlink() or bool(getattr(path, 'is_junction', lambda: False)())


def _hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(262144), b''):
            digest.update(chunk)
    return digest.hexdigest()


class STTManager:
    def __init__(self, root):
        self.root = Path(root).absolute()
        self._lock = threading.Lock()

    def _safe(self, path):
        path = Path(path).absolute()
        if '..' in path.parts or not path.is_relative_to(self.root) or not path.resolve().is_relative_to(self.root.resolve()):
            raise STTError('Путь находится вне каталога голосового ввода.')
        for item in (path, *path.parents):
            if _linked(item):
                raise STTError('Каталог голосового ввода содержит ссылку.')
        return path

    def _config(self):
        try:
            target = self._safe(self.root / 'components.json')
            if target.stat().st_size > 65536:
                return {}
            result = json.loads(target.read_text(encoding='utf-8'))
            return result if isinstance(result, dict) else {}
        except (OSError, ValueError, STTError):
            return {}

    def component_paths(self):
        config = self._config()
        try:
            executable, model = self._safe(self.root / config['executable']), self._safe(self.root / config['model'])
            return executable, model
        except (KeyError, TypeError, STTError):
            return None, None

    def status(self):
        executable, model = self.component_paths()
        ready = bool(executable and model and executable.is_file() and model.is_file())
        config = self._config()
        if ready:
            try:
                files = config['files']
                if not isinstance(files, dict) or not files or str(executable.relative_to(self.root)) not in files or str(model.relative_to(self.root)) not in files:
                    raise STTError('Не найдены контрольные суммы компонентов.')
                for relative, digest in files.items():
                    item = self._safe(self.root / relative)
                    if not item.is_file() or _hash(item) != digest:
                        ready = False
                        break
            except (KeyError, TypeError, OSError, STTError):
                ready = False
        supported = (platform.system() in ENGINES and platform.machine().lower() in ('amd64', 'x86_64')) or (platform.system() == 'Darwin' and bool(shutil.which('cmake')) and bool(shutil.which('clang++')))
        return dict(ready=ready, supported=supported, executable=executable, model=model,
                    message='Готов к работе' if ready else ('Требуется загрузка' if supported else 'Выберите локальный движок и модель: автоматическая установка для этой платформы пока недоступна.'))

    def _write_config(self, config):
        self._safe(self.root).mkdir(parents=True, exist_ok=True)
        temporary = self._safe(self.root / 'components.json.part')
        try:
            temporary.write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')
            os.replace(temporary, self._safe(self.root / 'components.json'))
        finally:
            temporary.unlink(missing_ok=True)

    def save_manual_paths(self, executable, model=None):
        if not self._lock.acquire(blocking=False):
            raise STTError('Установка уже выполняется.')
        try:
            return self._save_manual_paths(executable, model)
        except (OSError, ValueError, RuntimeError) as error:
            if isinstance(error, STTError):
                raise
            raise STTError('Не удалось импортировать компоненты. Проверьте выбранные файлы и доступ к папке приложения.') from error
        finally:
            self._lock.release()

    def _save_manual_paths(self, executable, model=None):
        _, old_model = self.component_paths()
        executable = Path(executable).resolve()
        model = Path(model).resolve() if model else old_model
        if not executable.is_file() or not model or not model.is_file():
            raise STTError('Выберите существующие файлы движка и модели Whisper.')
        try:
            self._safe(self.root).mkdir(parents=True, exist_ok=True)
            stage = Path(tempfile.mkdtemp(prefix='manual-', dir=self.root))
        except OSError as error:
            raise STTError('Не удаётся записать компоненты. Переместите папку приложения в доступный для записи каталог.') from error
        try:
            libraries = [p for p in executable.parent.iterdir() if p.is_file() and not _linked(p) and p.suffix.lower() in ('.dll', '.so', '.dylib')]
            sources = [executable, model, *libraries]
            if len(sources) > 128 or sum(p.stat().st_size for p in sources) > 512000000:
                raise STTError('Выбранные компоненты превышают допустимый размер 512 МБ.')
            copied = self._safe(stage / executable.name)
            shutil.copyfile(executable, copied)
            copied.chmod(executable.stat().st_mode & 0o755)
            # Only adjacent runtime libraries, never an entire user directory.
            for library in libraries:
                if library.name != executable.name:
                    shutil.copyfile(library, self._safe(stage / library.name))
            copied_model = self._safe(stage / 'ggml-tiny.bin')
            shutil.copyfile(model, copied_model)
            files = {str(p.relative_to(self.root)): _hash(p) for p in stage.iterdir() if p.is_file()}
            self._write_config(dict(manual=True, executable=str(copied.relative_to(self.root)), model=str(copied_model.relative_to(self.root)), files=files))
        except (OSError, ValueError) as error:
            self._safe(stage)
            shutil.rmtree(stage)
            raise STTError('Не удалось скопировать компоненты голосового ввода. Проверьте доступ к файлам и свободное место.') from error
        except STTError:
            self._safe(stage)
            shutil.rmtree(stage)
            raise
        return self.status()

    def _download(self, specification, target, cancelled, progress, offset, total):
        _validate_url(specification['url'])
        request = urllib.request.Request(specification['url'], headers={'User-Agent': 'OrangeNotes-STT/0.1.4'})
        opener = urllib.request.build_opener(_Redirect())
        digest = hashlib.sha256()
        size = 0
        with opener.open(request, timeout=16) as response, self._safe(target).open('xb') as stream:
            _validate_url(response.geturl())
            length = response.headers.get('Content-Length')
            if length and int(length) != specification['size']:
                raise STTError('Размер загрузки не совпадает с проверенным компонентом.')
            while True:
                if cancelled.is_set():
                    raise STTError('Загрузка отменена.')
                chunk = response.read(262144)
                if not chunk:
                    break
                size += len(chunk)
                if size > specification['size']:
                    raise STTError('Загруженный компонент превышает допустимый размер.')
                digest.update(chunk)
                stream.write(chunk)
                if progress:
                    progress(offset + size, total)
        if size != specification['size'] or digest.hexdigest() != specification['sha256']:
            raise STTError('Контрольная сумма компонента не совпадает. Установка отменена.')

    def _extract(self, archive, destination, cancelled):
        total = 0
        count = 0
        def target_for(name, size):
            nonlocal total, count
            path = PurePosixPath(name.replace('\\', '/'))
            if path.is_absolute() or '..' in path.parts or any(':' in part for part in path.parts):
                raise STTError('Архив содержит небезопасный путь.')
            count += 1
            total += size
            if size < 0 or count > 3000 or total > 250000000:
                raise STTError('Архив превышает допустимый размер.')
            return self._safe(destination.joinpath(*path.parts))
        def copy(source, target):
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as output:
                while True:
                    if cancelled.is_set():
                        raise STTError('Загрузка отменена.')
                    chunk = source.read(262144)
                    if not chunk:
                        break
                    output.write(chunk)
        if zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive) as bundle:
                for info in bundle.infolist():
                    target = target_for(info.filename, info.file_size)
                    mode = info.external_attr >> 16
                    if stat.S_ISLNK(mode):
                        raise STTError('Архив содержит ссылку.')
                    if not info.is_dir():
                        with bundle.open(info) as source:
                            copy(source, target)
        else:
            with tarfile.open(archive, 'r:gz') as bundle:
                for info in bundle:
                    target = target_for(info.name, info.size)
                    if info.isdir():
                        continue
                    if not info.isfile():
                        raise STTError('Архив содержит ссылку или специальный файл.')
                    with bundle.extractfile(info) as source:
                        copy(source, target)
                    target.chmod(info.mode & 0o755)

    def _build_macos(self, source_directory, destination, cancelled):
        cmake = shutil.which('cmake')
        compiler = shutil.which('clang++')
        if not cmake or not compiler:
            raise STTError('Для установки на macOS нужны CMake и инструменты разработчика Apple. Можно выбрать готовый локальный движок.')
        candidates = [p.parent for p in source_directory.rglob('CMakeLists.txt') if (p.parent / 'examples' / 'cli').is_dir()]
        if len(candidates) != 1:
            raise STTError('В архиве не найдены исходники движка Whisper.')
        source = candidates[0]
        build = source_directory.parent / 'build'
        commands = [
            [cmake, '-S', str(source), '-B', str(build), '-DCMAKE_BUILD_TYPE=Release',
             '-DBUILD_SHARED_LIBS=OFF', '-DGGML_METAL=OFF', '-DGGML_CUDA=OFF', '-DWHISPER_COREML=OFF', '-DWHISPER_FFMPEG=OFF', '-DCMAKE_CXX_COMPILER=' + compiler,
             '-DGGML_VULKAN=OFF', '-DGGML_BLAS=OFF', '-DGGML_ACCELERATE=OFF',
             '-DGGML_NATIVE=OFF', '-DWHISPER_BUILD_TESTS=OFF', '-DWHISPER_BUILD_EXAMPLES=ON'],
            [cmake, '--build', str(build), '--config', 'Release', '--target', 'whisper-cli', '--parallel', '2'],
        ]
        log_path = self._safe(source_directory.parent / 'build.log')
        for command in commands:
            if cancelled.is_set():
                raise STTError('Загрузка отменена.')
            with log_path.open('wb') as log:
                process = subprocess.Popen(command, stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
                deadline = time.monotonic() + 180
                try:
                    while process.poll() is None:
                        if cancelled.wait(0.1):
                            raise STTError('Загрузка отменена.')
                        if time.monotonic() >= deadline or log_path.stat().st_size > 1048576:
                            raise STTError('Сборка голосового движка остановлена: превышено допустимое время или размер журнала.')
                    if process.returncode:
                        raise STTError('Не удалось собрать движок Whisper. Проверьте инструменты разработчика Apple.')
                finally:
                    if process.poll() is None:
                        try:
                            os.killpg(process.pid, signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                        try:
                            process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            try:
                                os.killpg(process.pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                            process.wait(timeout=2)
        executable = build / 'bin' / 'whisper-cli'
        if not executable.is_file() or _linked(executable):
            raise STTError('Сборка не создала движок Whisper.')
        self._safe(destination).mkdir(parents=True, exist_ok=True)
        target = self._safe(destination / 'whisper-cli')
        shutil.copyfile(executable, target)
        target.chmod(0o755)
        # Only the static runtime is retained; temporary source/build output is removed.
        self._safe(source_directory)
        shutil.rmtree(source_directory)
        self._safe(build)
        shutil.rmtree(build)
        log_path.unlink(missing_ok=True)

    def install(self, cancel_event=None, progress=None):
        cancelled = cancel_event or threading.Event()
        if not self._lock.acquire(blocking=False):
            raise STTError('Установка уже выполняется.')
        stage = None
        try:
            if not self.status()['supported']:
                raise STTError('Автоматическая установка для этой платформы пока недоступна. Выберите локальный движок и модель.')
            if cancelled.is_set():
                raise STTError('Загрузка отменена.')
            self._safe(self.root).mkdir(parents=True, exist_ok=True)
            stage = Path(tempfile.mkdtemp(prefix='.install-', dir=self.root))
            engine = MAC_SOURCE if platform.system() == 'Darwin' else ENGINES[platform.system()]
            total = engine['size'] + MODEL['size']
            archive = stage / 'engine.part'
            self._download(engine, archive, cancelled, progress, 0, total)
            if platform.system() == 'Darwin':
                self._extract(archive, stage / 'source', cancelled)
                self._build_macos(stage / 'source', stage / 'engine', cancelled)
            else:
                self._extract(archive, stage / 'engine', cancelled)
            archive.unlink()
            model = stage / 'ggml-tiny.bin'
            self._download(MODEL, model, cancelled, progress, engine['size'], total)
            candidates = list((stage / 'engine').rglob('whisper-cli.exe' if platform.system() == 'Windows' else 'whisper-cli'))
            if len(candidates) != 1:
                raise STTError('В архиве отсутствует однозначно определённый движок Whisper.')
            executable = candidates[0]
            if platform.system() != 'Windows':
                executable.chmod(executable.stat().st_mode | 0o500)
            if cancelled.is_set():
                raise STTError('Загрузка отменена.')
            files = {}
            for item in stage.rglob('*'):
                if cancelled.is_set():
                    raise STTError('Загрузка отменена.')
                if item.is_file():
                    files[str(item.relative_to(stage))] = _hash(item)
            if cancelled.is_set():
                raise STTError('Загрузка отменена.')
            final = self._safe(self.root / ('pack-' + stage.name.removeprefix('.install-')))
            executable_relative = executable.relative_to(stage)
            os.replace(stage, final)
            stage = final
            self._write_config(dict(manual=False, executable=str(final.relative_to(self.root) / executable_relative), model=str(final.relative_to(self.root) / 'ggml-tiny.bin'), files={str(final.relative_to(self.root) / Path(name)): digest for name, digest in files.items()}))
            stage = None
            return self.status()
        except STTError:
            raise
        except (OSError, ValueError, tarfile.TarError, zipfile.BadZipFile) as error:
            raise STTError('Не удалось установить голосовой ввод. Проверьте соединение и свободное место.') from error
        finally:
            if stage is not None:
                self._safe(stage)
                shutil.rmtree(stage)
            self._lock.release()
