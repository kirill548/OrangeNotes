"""Wrap a verified Windows Lite onedir without changing its portable ZIP."""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile

if __package__:
    from .build import APP_VERSION
else:
    from build import APP_VERSION


ROOT = Path(__file__).resolve().parents[1]
SETUP_SCRIPT = ROOT / 'packaging/windows/OrangeNotes.iss'
SETUP_NAME = 'Orange Notes Setup.exe'
PRIVATE_DIRS = {'ai', 'stt', 'ai_packs', 'ollama_profile', 'models', 'runtime'}
PRIVATE_NAMES = {'settings.json', 'ai_settings.json', 'worker_status.json', 'app.lock'}


def compiler_path(explicit=None):
    if explicit:
        path = Path(explicit).resolve(strict=True)
        if not path.is_file():
            raise ValueError(f'ISCC is not a file: {path}')
        return path
    on_path = shutil.which('ISCC.exe')
    if on_path:
        return Path(on_path).resolve(strict=True)
    bases = [
        os.environ.get('ProgramFiles(x86)'),
        os.environ.get('ProgramFiles'),
        str(Path(os.environ['LOCALAPPDATA']) / 'Programs') if 'LOCALAPPDATA' in os.environ else None,
    ]
    for base in filter(None, bases):
        for major in (6, 7):
            candidate = Path(base) / f'Inno Setup {major}' / 'ISCC.exe'
            if candidate.is_file():
                return candidate.resolve(strict=True)
    raise ValueError('Inno Setup 6.3+ is required. Install it or pass --iscc PATH.')


def check_amd64_executable(path):
    with path.open('rb') as handle:
        header = handle.read(64)
        if len(header) == 64 and header.startswith(b'MZ'):
            handle.seek(struct.unpack_from('<I', header, 60)[0])
            if handle.read(6) == b'PE\0\0\x64\x86':
                return
    raise ValueError(f'Expected a Windows AMD64 PE executable: {path}')


def validate_payload(bundle):
    expected = bundle / 'OrangeNotes.exe'
    entry_files = (expected, bundle / 'edition.json', bundle / 'README.txt')
    resource_dirs = (bundle / '_internal', bundle / 'licenses')
    if not all(file.is_file() for file in entry_files) or not all(folder.is_dir() for folder in resource_dirs):
        raise ValueError('The Windows Lite onedir needs OrangeNotes.exe, edition.json, README.txt, _internal and licenses.')
    for path in (*entry_files, *resource_dirs, *(p for folder in resource_dirs for p in folder.rglob('*'))):
        if path.is_symlink() or path.is_junction():
            raise ValueError(f'Installer payload must not contain a reparse link: {path}')
        name = path.name.lower()
        if (
            name in PRIVATE_NAMES or (path.is_dir() and name in PRIVATE_DIRS)
            or name.endswith(('.db', '.sqlite', '.sqlite3', '.gguf', '.ggml', '.pem', '.key', '.p12'))
            or name.endswith(('.db-wal', '.db-shm', '.sqlite-wal', '.sqlite-shm', '.sqlite3-wal', '.sqlite3-shm'))
        ):
            raise ValueError(f'Local data is not allowed inside the installer payload: {path.relative_to(bundle)}')
    metadata = json.loads((bundle / 'edition.json').read_text(encoding='utf-8'))
    if metadata != {'edition': 'lite', 'model': None}:
        raise ValueError('Only the Lite onedir is eligible for the Windows installer.')
    check_amd64_executable(expected)
    if not (bundle / '_internal/app/assets/app.ico').is_file():
        raise ValueError('The Windows application icon is missing from the onedir.')


def build(bundle, output_dir, iscc, version):
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version):
        raise ValueError('The installer version must contain three numeric parts.')
    if bundle.is_symlink() or bundle.is_junction():
        raise ValueError('The input onedir must not be a reparse link.')
    bundle = bundle.resolve(strict=True)
    output_dir = output_dir.resolve()
    if output_dir.is_relative_to(bundle):
        raise ValueError('Write the installer outside the application onedir.')
    validate_payload(bundle)
    compiler = compiler_path(iscc)
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.windows-setup-', dir=output_dir) as scratch:
        subprocess.run(
            [str(compiler), f'/DBundleDir={bundle}', f'/DProductVersion={version}', f'/O{scratch}', str(SETUP_SCRIPT)],
            check=True,
        )
        candidate = Path(scratch) / SETUP_NAME
        if not candidate.is_file() or candidate.stat().st_size == 0:
            raise ValueError(f'Inno Setup did not create {SETUP_NAME}.')
        published = output_dir / SETUP_NAME
        candidate.replace(published)
    return published


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, default=ROOT / 'dist/OrangeNotes')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'dist')
    parser.add_argument('--iscc', type=Path, help='Explicit path to the installed Inno Setup command-line compiler')
    parser.add_argument('--version', default=APP_VERSION)
    args = parser.parse_args(argv)
    if sys.platform != 'win32':
        parser.error('Build the installer on Windows.')
    try:
        path = build(args.bundle, args.output_dir, args.iscc, args.version)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'{error}\n')
    print(path)


if __name__ == '__main__':
    main()
