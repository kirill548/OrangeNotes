"""Flatten trusted CI package artifacts, rejecting collisions and missing platforms."""
import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

ARTIFACTS = ("Windows-x64", "Linux-x64", "macOS-Intel", "macOS-AppleSilicon")
EXTENSIONS = (".zip", ".tar.gz", ".dmg", ".AppImage", ".deb", ".flatpak")


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def verified_manifest(folder, packages, commit):
    manifest_files = list(folder.rglob('CANDIDATE_MANIFEST.json'))
    if not manifest_files:
        raise FileNotFoundError('Candidate manifest missing: ' + folder.name)
    if len(manifest_files) != 1:
        raise ValueError('Ambiguous candidate manifests: ' + folder.name)
    manifest = json.loads(manifest_files[0].read_text(encoding='utf-8'))
    if manifest.get('version') != '0.2.1' or manifest.get('source_commit') != commit:
        raise ValueError('Candidate source/version mismatch: ' + folder.name)
    records = manifest.get('packages')
    if not isinstance(records, list):
        raise ValueError('Invalid candidate package records: ' + folder.name)
    by_name = {record['file']: record for record in records}
    if len(by_name) != len(records) or set(by_name) != {p.name for p in packages}:
        raise ValueError('Candidate package list mismatch: ' + folder.name)
    for package in packages:
        record = by_name[package.name]
        if record.get('size') != package.stat().st_size or record.get('sha256') != file_sha256(package):
            raise ValueError('Candidate checksum mismatch: ' + package.name)
    return manifest


def prepare(downloads, destination, commit=None):
    downloads, destination = Path(downloads), Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    if commit is not None and not re.fullmatch(r'[a-f0-9]{40}', commit):
        raise ValueError('A full lowercase Git commit SHA is required')
    manifests = {}
    assets = {}
    for platform in ARTIFACTS:
        folder = downloads / ("OrangeNotes-" + platform)
        packages = sorted(p for p in folder.rglob("OrangeNotes-*")
                          if p.is_file() and p.name.endswith(EXTENSIONS))
        if not packages:
            raise ValueError("Missing package artifacts: " + platform)
        if commit is not None:
            manifests[platform] = verified_manifest(folder, packages, commit)
        for package in packages:
            digest = file_sha256(package)
            if package.name in assets and assets[package.name] != digest:
                raise ValueError("Conflicting release filename: " + package.name)
            assets[package.name] = digest
            shutil.copyfile(package, destination / package.name)
    linux = downloads / "OrangeNotes-Linux-x64"
    for suffix in (".deb", ".AppImage", ".flatpak"):
        if not any(p.name.endswith(suffix) for p in linux.rglob("OrangeNotes-*")):
            raise ValueError("Missing Linux release format: " + suffix)
    if commit is not None:
        provenance = {'version': '0.2.1', 'source_commit': commit,
                      'native_build_manifests': manifests}
        (destination / 'RELEASE_PROVENANCE.json').write_text(
            json.dumps(provenance, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return assets


def checksums(destination):
    destination = Path(destination)
    files = sorted(p for p in destination.iterdir() if p.is_file() and p.name != "SHA256SUMS.txt")
    text = "".join(file_sha256(p) + "  " + p.name + "\n" for p in files)
    (destination / "SHA256SUMS.txt").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("downloads")
    parser.add_argument("destination")
    parser.add_argument("--commit", help="Require native candidate manifests for this exact commit")
    args = parser.parse_args()
    prepare(args.downloads, args.destination, args.commit)
    checksums(args.destination)
