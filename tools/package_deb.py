"""Package a frozen Linux directory; never invoke sudo or install the package."""
import argparse
import pathlib
import shutil
import subprocess
import tempfile


def package(folder, destination, version, architecture):
    if architecture not in ('amd64', 'arm64'):
        raise ValueError('Only tested package architecture names are accepted')
    if not version or any(c not in '0123456789.-+' for c in version):
        raise ValueError('Version must be a Debian-compatible numeric release')
    folder, destination = pathlib.Path(folder).resolve(), pathlib.Path(destination).resolve()
    if not (folder / 'OrangeNotes').is_file():
        raise ValueError('Frozen OrangeNotes executable is missing')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='orangenotes-deb-') as temporary:
        root = pathlib.Path(temporary)
        shutil.copytree(folder, root / 'opt/orangenotes')
        (root / 'DEBIAN').mkdir()
        (root / 'DEBIAN/control').write_text(f'Package: orange-notes\nVersion: {version}\nArchitecture: {architecture}\nMaintainer: Orange Notes contributors\nDepends: libnotify-bin, libglib2.0-bin, libegl1, libopengl0, libxcb-cursor0, libxkbcommon-x11-0\nDescription: Local notes and durable reminders\n', encoding='utf-8')
        bin_path = root / 'usr/bin'
        bin_path.mkdir(parents=True)
        launcher = bin_path / 'orange-notes'
        launcher.write_text('#!/bin/sh\nexec /opt/orangenotes/OrangeNotes "$@"\n', encoding='utf-8')
        launcher.chmod(0o755)
        desktop = root / 'usr/share/applications'
        desktop.mkdir(parents=True)
        (desktop / 'orange-notes.desktop').write_text('[Desktop Entry]\nType=Application\nName=Orange Notes\nExec=orange-notes\nIcon=orange-notes\nCategories=Office;Utility;\nTerminal=false\n', encoding='utf-8')
        icons = root / 'usr/share/icons/hicolor/256x256/apps'
        icons.mkdir(parents=True)
        shutil.copy2(folder / 'OrangeNotes.png', icons / 'orange-notes.png')
        subprocess.run(['dpkg-deb', '--build', '--root-owner-group', str(root), str(destination)], check=True)
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('folder')
    parser.add_argument('destination')
    parser.add_argument('--version', default='0.1.0')
    parser.add_argument('--architecture', default='amd64', choices=['amd64', 'arm64'])
    args = parser.parse_args()
    package(args.folder, args.destination, args.version, args.architecture)
