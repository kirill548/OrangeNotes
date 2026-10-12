"""Build on the target operating system; never include user databases."""
import platform
import os
import shutil
import subprocess
import sys
import argparse
import plistlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def bundle_directory(target=None, output_directory=None):
    return Path(output_directory or ROOT / 'dist') / ('OrangeNotes.app' if (target or sys.platform) == 'darwin' else 'OrangeNotes')


def mac_icon():
    """Create an Apple icon using the native tool; no imaging dependency."""
    iconset = ROOT / 'build/OrangeNotes.iconset'
    iconset.mkdir(parents=True, exist_ok=True)
    source = ROOT / 'app/assets/app.png'
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            suffix = '@2x' if scale == 2 else ''
            output = iconset / f'icon_{size}x{size}{suffix}.png'
            subprocess.run(['sips', '-z', str(size * scale), str(size * scale),
                            str(source), '--out', str(output)], check=True, stdout=subprocess.DEVNULL)
    output = ROOT / 'build/OrangeNotes.icns'
    subprocess.run(['iconutil', '-c', 'icns', str(iconset), '-o', str(output)], check=True)
    return output


def prepare_bundle(folder, target=None):
    """Finish bundle contents before code signing seals macOS resources."""
    target = target or sys.platform
    resources = folder / 'Contents/Resources' if target == 'darwin' else folder
    resources.mkdir(parents=True, exist_ok=True)
    component_root = folder / 'Contents/MacOS' if target == 'darwin' else folder
    for component in ('ai', 'stt'):
        (component_root / component).mkdir(parents=True, exist_ok=True)
    if target == 'darwin':
        plist_path = folder / 'Contents/Info.plist'
        metadata = plistlib.loads(plist_path.read_bytes()) if plist_path.exists() else {}
        metadata['NSMicrophoneUsageDescription'] = (
            'Микрофон нужен для голосового ввода заметок. Аудио распознаётся на Вашем компьютере.')
        plist_path.write_bytes(plistlib.dumps(metadata))
    shutil.copy2(ROOT / 'DISTRIBUTION.md', resources / 'README.txt')
    licenses = ROOT / 'licenses'
    if licenses.exists():
        shutil.copytree(licenses, resources / 'licenses', dirs_exist_ok=True)
    if target.startswith('linux'):
        shutil.copy2(ROOT / 'app/assets/app.png', folder / 'OrangeNotes.png')
        (folder / 'OrangeNotes.desktop').write_text(
            '[Desktop Entry]\nType=Application\nName=Orange Notes\n'
            'Comment=Local notes and reminders\nExec=OrangeNotes\n'
            'Icon=OrangeNotes\nTerminal=false\nCategories=Office;Utility;\n', encoding='utf-8')


def package_bundle(folder, target=None, *, prepared=False):
    target = target or sys.platform
    if not prepared:
        prepare_bundle(folder, target)
    name = 'OrangeNotes-' + platform.system() + '-' + platform.machine()
    # tar preserves executable permissions on Linux. ditto preserves macOS bundle metadata.
    if target == 'darwin':
        archive = str(folder.parent / (name + '.zip'))
        subprocess.run(['ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', str(folder), archive], check=True)
        return archive
    return shutil.make_archive(str(folder.parent / name),
                               'gztar' if target.startswith('linux') else 'zip',
                               root_dir=folder.parent, base_dir=folder.name)


def main(argv=None):
    if sys.maxsize <= 2**32:
        raise SystemExit('Qt 6 requires a supported 64-bit build environment.')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--clean', action='store_true')
    parser.add_argument('--edition', choices=('lite', 'complete'), default='lite')
    parser.add_argument('--ai-pack', type=Path, help='Verified native Ollama seed for Complete')
    parser.add_argument('--dist-dir', type=Path, default=None)
    options = parser.parse_args(argv)
    output_directory = (options.dist_dir or (ROOT/'dist/complete' if options.edition=='complete' else ROOT/'dist')).resolve()
    pack_manifest = None
    if options.edition == 'complete':
        if options.ai_pack is None:
            parser.error('--edition complete requires --ai-pack')
        pack_root=options.ai_pack.resolve()
        for generated in (ROOT/'build', bundle_directory(output_directory=output_directory)):
            generated=generated.resolve()
            if pack_root.is_relative_to(generated) or generated.is_relative_to(pack_root):
                parser.error('--ai-pack must not overlap generated build or bundle directories')
        from app.services.bundled_ai import validate_pack
        pack_manifest = validate_pack(options.ai_pack)
    elif options.ai_pack is not None:
        parser.error('--ai-pack is only supported by Complete')
    args = [sys.executable, '-m', 'PyInstaller', '--noconfirm',
            '--onedir', '--windowed', '--name', 'OrangeNotes',
            '--paths', str(ROOT), '--distpath', str(output_directory),
            '--workpath', str(ROOT / 'build'), '--specpath', str(ROOT / 'build'),
            '--copy-metadata', 'PySide6_Essentials', '--copy-metadata', 'shiboken6',
            '--collect-data', 'tzdata',
            # Audio capture is optional at runtime. Explicit inclusion also runs
            # PyInstaller's QtMultimedia hook for platform multimedia plugins.
            '--hidden-import', 'PySide6.QtMultimedia',
            '--add-data', str(ROOT / 'app/assets') + ':app/assets',
            '--add-data', str(ROOT / 'app/services/windows_toast.ps1') + ':app/services']
    if sys.platform == 'win32':
        args += ['--icon', str(ROOT / 'app/assets/app.ico')]
    elif sys.platform == 'darwin':
        args += ['--icon', str(mac_icon()), '--osx-bundle-identifier', 'org.orangenotes.desktop']
    args.append(str(ROOT / 'app/main.py'))
    if options.clean:
        args.insert(3, '--clean')
    env = os.environ.copy()
    if sys.platform == 'win32':
        import PySide6
        windows = Path(os.environ.get('SystemRoot', 'C:/Windows'))
        # Do not collect incompatible DLLs from unrelated software on PATH.
        env['PATH'] = os.pathsep.join([str(Path(PySide6.__file__).parent),
                                     str(Path(sys.executable).parent),
                                     str(windows / 'System32'), str(windows)])
    subprocess.run(args, cwd=ROOT, env=env, check=True)
    # Load the sibling tool explicitly: callers may import this file by path.
    import importlib.util
    native_spec=importlib.util.spec_from_file_location('orange_notes_release_native',Path(__file__).resolve().with_name('release_native.py'))
    native=importlib.util.module_from_spec(native_spec)
    native_spec.loader.exec_module(native)
    folder=bundle_directory(output_directory=output_directory)
    prepare_bundle(folder)
    resources = folder / 'Contents/Resources' if sys.platform == 'darwin' else folder
    import json
    (resources/'edition.json').write_text(json.dumps({'edition':options.edition, 'model':(pack_manifest or {}).get('model')}), encoding='utf-8')
    if pack_manifest is not None:
        shutil.copytree(options.ai_pack, resources/'bundled_ai')
    native.sign_bundle(folder)
    archive = package_bundle(folder, prepared=True)
    if options.edition == 'complete':
        original=Path(archive)
        suffix=''.join(original.suffixes)
        renamed=original.with_name(original.name[:-len(suffix)]+'-Complete'+suffix)
        original.replace(renamed)
        archive=str(renamed)
    if sys.platform=='darwin':
        native.mac_dmg(folder,output_directory/('OrangeNotes-macOS-'+platform.machine()+('-Complete' if options.edition=='complete' else '')+'.dmg'))
    if sys.platform.startswith('linux'):
        native.linux_appimage(folder,output_directory/('OrangeNotes-Linux-'+platform.machine()+('-Complete' if options.edition=='complete' else '')+'.AppImage'))
    print(archive)


if __name__ == '__main__':
    main()
