"""Build on the target operating system; never include user databases."""
import platform
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def bundle_directory(target=None):
    return ROOT / 'dist' / ('OrangeNotes.app' if (target or sys.platform) == 'darwin' else 'OrangeNotes')


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


def package_bundle(folder, target=None):
    target = target or sys.platform
    resources = folder / 'Contents/Resources' if target == 'darwin' else folder
    resources.mkdir(parents=True, exist_ok=True)
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
    name = 'OrangeNotes-' + platform.system() + '-' + platform.machine()
    # tar preserves executable permissions on Linux. ditto preserves macOS bundle metadata.
    if target == 'darwin':
        archive = str(ROOT / 'dist' / (name + '.zip'))
        subprocess.run(['ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', str(folder), archive], check=True)
        return archive
    return shutil.make_archive(str(ROOT / 'dist' / name),
                               'gztar' if target.startswith('linux') else 'zip',
                               root_dir=folder.parent, base_dir=folder.name)


def main():
    if sys.maxsize <= 2**32:
        raise SystemExit('Qt 6 requires a supported 64-bit build environment.')
    args = [sys.executable, '-m', 'PyInstaller', '--noconfirm',
            '--onedir', '--windowed', '--name', 'OrangeNotes',
            '--paths', str(ROOT), '--distpath', str(ROOT / 'dist'),
            '--workpath', str(ROOT / 'build'), '--specpath', str(ROOT / 'build'),
            '--copy-metadata', 'PySide6_Essentials', '--copy-metadata', 'shiboken6',
            '--collect-data', 'tzdata',
            '--add-data', str(ROOT / 'app/assets') + ':app/assets',
            '--add-data', str(ROOT / 'app/services/windows_toast.ps1') + ':app/services']
    if sys.platform == 'win32':
        args += ['--icon', str(ROOT / 'app/assets/app.ico')]
    elif sys.platform == 'darwin':
        args += ['--icon', str(mac_icon()), '--osx-bundle-identifier', 'org.orangenotes.desktop']
    args.append(str(ROOT / 'app/main.py'))
    if '--clean' in sys.argv:
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
    folder=bundle_directory()
    native.sign_bundle(folder)
    archive = package_bundle(folder)
    if sys.platform=='darwin':
        native.mac_dmg(folder,ROOT/'dist'/('OrangeNotes-macOS-'+platform.machine()+'.dmg'))
    if sys.platform.startswith('linux'):
        native.linux_appimage(folder,ROOT/'dist'/('OrangeNotes-Linux-'+platform.machine()+'.AppImage'))
    print(archive)


if __name__ == '__main__':
    main()
